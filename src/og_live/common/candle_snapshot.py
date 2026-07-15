"""Parse DP candle snapshot events and state keys into standard OHLCV frames."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

import pandas as pd

from og_core.engine import OHLCV_COLUMNS, normalize_ohlcv_frame

_REQUIRED_BAR_FIELDS = {"bar_time", "open", "high", "low", "close", "volume"}


class MalformedSnapshotError(ValueError):
    """Raised when a candle_snapshot entry cannot be interpreted safely."""


@dataclass(frozen=True)
class SnapshotEvent:
    """One DP stream event that points OG to the updated snapshot state key."""

    event_type: str
    symbol: str
    tf: str
    bar_time: str
    state_key: str
    snapshot_version: str
    published_at_utc: str | None = None
    bars_count: int | None = None
    symbol_id: int | None = None
    schema_version: str | None = None


@dataclass(frozen=True)
class CandleSnapshot:
    """Latest DP state snapshot for one symbol/timeframe pair."""

    symbol: str
    tf: str
    latest_bar_time: str
    snapshot_version: str
    bars_count: int
    bars: pd.DataFrame
    generated_at_utc: str | None = None
    schema_version: int | None = None


def normalize_fields(fields: dict[Any, Any]) -> dict[str, str]:
    """Convert Redis field/value bytes or strings to plain strings."""
    out: dict[str, str] = {}
    for key, value in fields.items():
        key_text = key.decode("utf-8") if isinstance(key, bytes) else str(key)
        value_text = value.decode("utf-8") if isinstance(value, bytes) else str(value)
        out[key_text] = value_text
    return out


def parse_snapshot_event(fields: dict[str, str]) -> SnapshotEvent:
    """Parse one DP `snapshot_updated` stream event."""
    symbol = _required_text(fields, "tv_symbol").upper()
    tf = _required_text(fields, "tf_code").upper()
    event = SnapshotEvent(
        event_type=_required_text(fields, "event_type"),
        symbol=symbol,
        tf=tf,
        bar_time=_required_text(fields, "bar_time"),
        state_key=_required_text(fields, "state_key"),
        snapshot_version=_required_text(fields, "snapshot_version"),
        published_at_utc=_optional_text(fields, "published_at_utc"),
        bars_count=_optional_int(fields, "bars_count"),
        symbol_id=_optional_int(fields, "symbol_id"),
        schema_version=_optional_text(fields, "schema_version"),
    )
    if event.event_type != "snapshot_updated":
        raise MalformedSnapshotError(f"unsupported event_type={event.event_type!r}")
    return event


def parse_state_snapshot(raw_snapshot: str) -> CandleSnapshot:
    """Parse one DP state-key JSON snapshot into normalized OHLCV bars."""
    try:
        data = json.loads(raw_snapshot)
    except json.JSONDecodeError as exc:
        raise MalformedSnapshotError(f"invalid snapshot JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise MalformedSnapshotError("snapshot JSON must be an object")

    records = data.get("bars")
    if not isinstance(records, list) or not records:
        raise MalformedSnapshotError("'bars' must be a non-empty JSON array")

    df = pd.DataFrame.from_records(records)
    missing = _REQUIRED_BAR_FIELDS - set(df.columns)
    if missing:
        raise MalformedSnapshotError(f"'bars' missing fields: {sorted(missing)}")

    df = df.rename(columns={"bar_time": "bartime"})
    bars = normalize_ohlcv_frame(df.loc[:, OHLCV_COLUMNS])
    symbol = _required_text(data, "tv_symbol").upper()
    tf = _required_text(data, "tf_code").upper()
    latest_bar_time = _required_text(data, "latest_bar_time")
    snapshot_version = _required_text(data, "snapshot_version")
    bars_count = _optional_int(data, "bars_count")
    return CandleSnapshot(
        symbol=symbol,
        tf=tf,
        latest_bar_time=latest_bar_time,
        snapshot_version=snapshot_version,
        bars_count=bars_count if bars_count is not None else len(bars),
        bars=bars,
        generated_at_utc=_optional_text(data, "generated_at_utc"),
        schema_version=_optional_int(data, "schema_version"),
    )


def snapshot_matches_event(snapshot: CandleSnapshot, event: SnapshotEvent) -> bool:
    """Return True when the latest state still represents the triggering event."""
    return (
        snapshot.symbol == event.symbol
        and snapshot.tf == event.tf
        and _canonical_time(snapshot.latest_bar_time) == _canonical_time(event.bar_time)
        and snapshot.snapshot_version == event.snapshot_version
    )


def _required_text(mapping: dict[str, Any], field: str) -> str:
    value = mapping.get(field)
    text = str(value if value is not None else "").strip()
    if text.lower() in {"", "none", "null"}:
        raise MalformedSnapshotError(f"missing {field!r}")
    return text


def _optional_text(mapping: dict[str, Any], field: str) -> str | None:
    value = mapping.get(field)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(mapping: dict[str, Any], field: str) -> int | None:
    value = mapping.get(field)
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise MalformedSnapshotError(f"invalid integer {field!r}: {value!r}") from exc


def _canonical_time(value: str) -> str:
    try:
        return pd.Timestamp(value).isoformat()
    except ValueError:
        return value
